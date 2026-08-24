import { FormEvent, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";

export function AdminUpload() {
  const navigate = useNavigate();
  const [categories, setCategories] = useState<Array<{ value: string; label: string }>>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.categories().then(setCategories).catch(() => setCategories([]));
  }, []);

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    const form = new FormData(event.currentTarget);
    try {
      const created = await api.upload(form);
      navigate(`/admin-panel/documents/${created.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="admin-page-head">
        <div>
          <h1 className="page-title">Upload policy document</h1>
          <p className="page-subtitle">Add an official PDF, Word, or text file to the knowledge base.</p>
        </div>
      </div>
      <form className="panel" onSubmit={onSubmit}>
        {error ? <div className="error">{error}</div> : null}
        <label>Title</label>
        <input name="title" required placeholder="Attendance Policy" />
        <label>Category</label>
        <select name="category" defaultValue="general">
          {categories.map((item) => (
            <option value={item.value} key={item.value}>
              {item.label}
            </option>
          ))}
        </select>
        <label>Department</label>
        <input name="department" placeholder="Academics" />
        <label>Version</label>
        <input name="version" defaultValue="1.0" />
        <label>Description</label>
        <textarea name="description" placeholder="Short summary of this policy" />
        <label>File (PDF, DOCX, or TXT)</label>
        <input name="file" type="file" accept=".pdf,.docx,.txt,application/pdf,text/plain" required />
        <button className="btn btn-gold" style={{ marginTop: 18 }} disabled={busy}>
          {busy ? "Uploading…" : "Upload and process"}
        </button>
      </form>
    </div>
  );
}
