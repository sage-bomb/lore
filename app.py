from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse
import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

# ---------------- Chroma setup ----------------
client = chromadb.PersistentClient(path="./chroma_db")
embed_fn = SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")

col = client.get_or_create_collection(
    name="notes",
    embedding_function=embed_fn,
    metadata={"hnsw:space": "cosine"},
)

app = FastAPI()


def page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>{title}</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin: 24px; max-width: 900px; }}
    nav a {{ margin-right: 12px; }}
    textarea {{ width: 100%; }}
    input[type="text"], input[type="number"] {{ width: 100%; padding: 8px; }}
    .card {{ border: 1px solid #ddd; border-radius: 12px; padding: 14px; margin: 12px 0; }}
    code {{ background: #f6f6f6; padding: 2px 6px; border-radius: 6px; }}
    .muted {{ color: #666; font-size: 0.9em; }}
    button {{ padding: 10px 14px; border-radius: 10px; border: 1px solid #ccc; cursor: pointer; }}
  </style>
</head>
<body>
  <h1>🧠 Tiny ChromaDB UI (FastAPI)</h1>
  <nav>
    <a href="/">Home</a>
    <a href="/browse">Browse</a>
  </nav>
  <hr/>
  {body}
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def home():
    body = """
    <h2>Add a document</h2>
    <form method="post" action="/add">
      <label class="muted">id (optional)</label>
      <input type="text" name="doc_id" placeholder="doc_123"/><br/><br/>

      <label class="muted">metadata key (optional)</label>
      <input type="text" name="meta_k" placeholder="source"/><br/><br/>

      <label class="muted">metadata value (optional)</label>
      <input type="text" name="meta_v" placeholder="notes"/><br/><br/>

      <label class="muted">text</label>
      <textarea name="text" rows="6" placeholder="Paste something here..." required></textarea><br/><br/>

      <button type="submit">Add / Upsert</button>
    </form>

    <hr/>

    <h2>Semantic search</h2>
    <form method="post" action="/search">
      <label class="muted">query</label>
      <input type="text" name="q" placeholder="What are we storing?" required/><br/><br/>

      <label class="muted">top_k</label>
      <input type="number" name="k" min="1" max="50" value="5"/><br/><br/>

      <button type="submit">Search</button>
    </form>
    """
    return page("Tiny ChromaDB UI", body)


@app.post("/add")
def add(
    text: str = Form(...),
    doc_id: str = Form(""),
    meta_k: str = Form(""),
    meta_v: str = Form(""),
):
    text = text.strip()
    if not text:
        return RedirectResponse(url="/", status_code=303)

    if not doc_id.strip():
        doc_id = f"doc_{abs(hash(text))}"  # simple stable-ish id

    metadata = {}
    if meta_k.strip():
        metadata[meta_k.strip()] = meta_v

    col.upsert(
        ids=[doc_id],
        documents=[text],
        metadatas=[metadata] if metadata else None,
    )
    return RedirectResponse(url="/browse", status_code=303)


@app.post("/search", response_class=HTMLResponse)
def search(q: str = Form(...), k: int = Form(5)):
    q = q.strip()
    k = max(1, min(int(k), 50))

    res = col.query(query_texts=[q], n_results=k)
    ids = res.get("ids", [[]])[0]
    docs = res.get("documents", [[]])[0]
    dists = res.get("distances", [[]])[0]
    metas = res.get("metadatas", [[]])[0]

    if not ids:
        body = f"""
        <h2>Search results</h2>
        <p class="muted">No results yet. Add some docs first.</p>
        <p><a href="/">Back</a></p>
        """
        return page("Search", body)

    cards = []
    for i, (rid, rdoc, dist, meta) in enumerate(zip(ids, docs, dists, metas), start=1):
        meta_html = ""
        if meta:
            meta_html = "<div class='muted'>metadata: <code>" + (
                str(meta).replace("<", "&lt;").replace(">", "&gt;")
            ) + "</code></div>"

        safe_doc = (rdoc or "").replace("<", "&lt;").replace(">", "&gt;")
        cards.append(f"""
        <div class="card">
          <div><b>{i}.</b> <code>{rid}</code> <span class="muted">(distance: {dist:.4f})</span></div>
          {meta_html}
          <pre style="white-space:pre-wrap;margin-top:10px;">{safe_doc}</pre>
        </div>
        """)

    body = f"""
    <h2>Search results</h2>
    <div class="muted">Query: <code>{q.replace("<","&lt;").replace(">","&gt;")}</code> | top_k: {k}</div>
    {''.join(cards)}
    <p><a href="/">Back</a></p>
    """
    return page("Search", body)


@app.get("/browse", response_class=HTMLResponse)
def browse(limit: int = 25):
    limit = max(1, min(int(limit), 200))
    got = col.get(limit=limit)
    ids = got.get("ids", [])
    docs = got.get("documents", [])
    metas = got.get("metadatas", [])

    if not ids:
        body = """
        <h2>Browse</h2>
        <p class="muted">Database is empty. Add something on the home page.</p>
        <p><a href="/">Add a doc</a></p>
        """
        return page("Browse", body)

    cards = []
    for rid, rdoc, meta in zip(ids, docs, metas):
        meta_html = ""
        if meta:
            meta_html = "<div class='muted'>metadata: <code>" + (
                str(meta).replace("<", "&lt;").replace(">", "&gt;")
            ) + "</code></div>"

        safe_doc = (rdoc or "").replace("<", "&lt;").replace(">", "&gt;")
        cards.append(f"""
        <div class="card">
          <div><code>{rid}</code></div>
          {meta_html}
          <pre style="white-space:pre-wrap;margin-top:10px;">{safe_doc}</pre>
        </div>
        """)

    body = f"""
    <h2>Browse</h2>
    <div class="muted">Showing up to {limit} items. Tip: <code>/browse?limit=100</code></div>
    {''.join(cards)}
    <p><a href="/">Back</a></p>
    """
    return page("Browse", body)
