import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type DocumentRecord } from "../api";

export function AdminDocuments() {
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [categories, setCategories] = useState<Array<{ value: string; label: string }>>([]);
  const [error, setError] = useState("");

  const load = async () => {
    const params = new URLSearchParams();
    if (search) params.set("search", search);
    if (category) params.set("category", category);
    const query = params.toString() ? `?${params}` : "";
    setDocuments(await api.documents(query));
  };

  useEffect(() => {
    api.categories().then(setCategories).catch(() => setCategories([]));
    load().catch((err) => setError(err instanceof Error ? err.message : "Could not load documents."));
  }, []);

  const remove = async (id: number) => {
    if (!confirm("Delete this policy document and its embeddings?")) return;
    await api.deleteDocument(id);
    await load();
  };

  return (
    <div>
      <div className="admin-page-head">
        <div>
          <h1 className="page-title">Policy documents</h1>
          <p className="page-subtitle">Search, filter, and maintain the official knowledge base.</p>
        </div>
      </div>
      <div className="panel">
        <div className="toolbar">
          <input
            placeholder="Search title, department, description"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <select value={category} onChange={(event) => setCategory(event.target.value)}>
            <option value="">All categories</option>
            {categories.map((item) => (
              <option value={item.value} key={item.value}>
                {item.label}
              </option>
            ))}
          </select>
          <button className="btn btn-navy" onClick={() => void load()}>
            Filter
          </button>
          <Link className="btn btn-gold" to="/admin-panel/upload">
            Upload document
          </Link>
        </div>
        {error ? <div className="error">{error}</div> : null}
        <table>
          <thead>
            <tr>
              <th>Title</th>
              <th>Category</th>
              <th>Version</th>
              <th>Status</th>
              <th>Chunks</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {documents.map((doc) => (
              <tr key={doc.id}>
                <td>
                  <Link to={`/admin-panel/documents/${doc.id}`}>{doc.title}</Link>
                </td>
                <td>{doc.category_label}</td>
                <td>{doc.version}</td>
                <td>
                  <span className={`badge ${doc.status}`}>{doc.status_label}</span>
                </td>
                <td>{doc.chunk_count}</td>
                <td>
                  <button className="btn btn-danger" onClick={() => void remove(doc.id)}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
