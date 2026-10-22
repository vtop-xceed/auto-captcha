#!/usr/bin/env python3
"""
Flask app for verifying and fixing captcha labels.
Usage: python scripts/verify_labels.py [--port 5050]
"""

import os
import sys
import glob
import json
from flask import Flask, send_file, jsonify, request

app = Flask(__name__)
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Label Verifier</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'Courier New', monospace;
    background: #111; color: #ccc;
    height: 100vh; display: flex; flex-direction: column;
    overflow: hidden; user-select: none;
  }

  /* Top bar */
  #topbar {
    display: flex; align-items: center; gap: 12px;
    padding: 10px 16px; background: #1a1a1a;
    border-bottom: 1px solid #333;
  }
  #topbar label { font-size: 13px; color: #888; }
  #folder-select {
    background: #222; color: #ccc; border: 1px solid #444;
    padding: 6px 10px; font-family: inherit; font-size: 13px;
    outline: none; cursor: pointer;
  }
  #folder-select:focus { border-color: #0af; }
  #counter { margin-left: auto; font-size: 13px; color: #666; }

  /* Main viewer */
  #viewer {
    flex: 1; display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    padding: 20px; position: relative;
  }

  /* Image + Label container */
  #img-container {
    display: inline-flex; flex-direction: column;
    border: 2px solid #555; background: #1a1a1a;
  }
  
  /* Image */
  #captcha-img {
    max-height: 50vh; max-width: 90vw;
    image-rendering: pixelated; display: block;
  }

  /* Label sits directly below the image */
  #current-label {
    display: flex; align-items: center; justify-content: center;
    padding: 8px 12px; font-size: 32px; font-weight: bold;
    color: #fff; letter-spacing: 8px; text-align: center;
    border-top: 2px solid #555; background: #000;
    white-space: nowrap; overflow: hidden;
  }
  #current-label.editing {
    color: #f0c040; background: #2a2000;
  }

  /* Counter below image */
  #img-counter {
    font-size: 13px; color: #555; margin-top: 8px;
    text-align: center;
  }

  /* Navigation */
  #nav {
    display: flex; gap: 8px; align-items: center; margin-top: 12px;
  }
  .nav-btn {
    background: #222; color: #ccc; border: 1px solid #444;
    padding: 8px 20px; font-family: inherit; font-size: 14px;
    cursor: pointer; transition: background 0.1s;
  }
  .nav-btn:hover { background: #333; }
  .nav-btn:active { background: #444; }

  /* Mode indicator */
  #mode-badge {
    font-size: 11px; padding: 3px 10px; margin-top: 8px;
    border: 1px solid #444; color: #888; background: #1a1a1a;
  }
  #mode-badge.edit-mode {
    border-color: #f0c040; color: #f0c040; background: #2a2000;
  }
  #mode-badge.search-mode {
    border-color: #0af; color: #0af; background: #001a2a;
  }

  /* Status toast */
  #toast {
    position: fixed; bottom: 20px; left: 50%;
    transform: translateX(-50%); padding: 8px 20px;
    background: #0af; color: #000; font-size: 13px;
    font-weight: bold; opacity: 0; transition: opacity 0.3s;
    pointer-events: none;
  }
  #toast.show { opacity: 1; }

  /* Hints */
  #hints {
    position: fixed; bottom: 10px; right: 16px;
    font-size: 11px; color: #555;
  }
  kbd {
    background: #222; border: 1px solid #444;
    padding: 1px 5px; font-size: 11px; color: #888;
  }
</style>
</head>
<body>

<div id="topbar">
  <label>Folder:</label>
  <select id="folder-select" tabindex="-1"></select>
  <span id="counter">-</span>
</div>

<div id="viewer">
  <div id="img-container">
    <img id="captcha-img" src="" alt="captcha" />
    <span id="current-label">-</span>
  </div>
  <div id="img-counter">-</div>
  <span id="mode-badge">NAVIGATE</span>
  <div id="nav">
    <button class="nav-btn" id="btn-prev" tabindex="-1">← j</button>
    <button class="nav-btn" id="btn-next" tabindex="-1">k →</button>
  </div>
</div>

<div id="toast"></div>
<div id="hints">
  <kbd>j</kbd> <kbd>k</kbd> navigate &nbsp;
  <kbd>Tab</kbd> rename &nbsp;
  <kbd>/</kbd> jump &nbsp;
  <kbd>r</kbd> delete &nbsp;
  <kbd>Enter</kbd> confirm &nbsp;
  <kbd>Esc</kbd> cancel
</div>

<script>
const $ = s => document.querySelector(s);
let images = [];
let idx = 0;
let mode = 'nav'; // 'nav' | 'edit' | 'search'
let typeBuf = '';

async function loadFolders() {
  const res = await fetch('/api/folders');
  const folders = await res.json();
  const sel = $('#folder-select');
  sel.innerHTML = '<option value="">— select —</option>';
  folders.forEach(f => {
    const opt = document.createElement('option');
    opt.value = f; opt.textContent = f;
    sel.appendChild(opt);
  });
}

async function loadImages(folder) {
  const res = await fetch('/api/images?folder=' + encodeURIComponent(folder));
  images = await res.json();
  idx = 0;
  render();
}

function label() {
  if (!images.length) return '-';
  const name = images[idx].split('/').pop();
  return name.substring(0, name.lastIndexOf('.'));
}

function render() {
  if (!images.length) {
    $('#captcha-img').src = '';
    $('#current-label').textContent = 'No images';
    $('#counter').textContent = '-';
    $('#img-counter').textContent = '-';
    return;
  }
  $('#captcha-img').src = '/api/image?path=' + encodeURIComponent(images[idx]) + '&t=' + Date.now();
  $('#counter').textContent = (idx + 1) + ' / ' + images.length;
  $('#img-counter').textContent = (idx + 1) + ' / ' + images.length;

  const badge = $('#mode-badge');
  const lbl = $('#current-label');
  badge.classList.remove('edit-mode', 'search-mode');
  lbl.classList.remove('editing');

  if (mode === 'edit') {
    lbl.textContent = typeBuf + '█';
    lbl.classList.add('editing');
    badge.textContent = 'RENAME'; badge.classList.add('edit-mode');
  } else if (mode === 'search') {
    lbl.textContent = '/' + typeBuf + '█';
    lbl.classList.add('editing');
    badge.textContent = 'SEARCH'; badge.classList.add('search-mode');
  } else {
    lbl.textContent = label();
    badge.textContent = 'NAVIGATE';
  }
}

function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 1200);
}

async function doRename(newLabel) {
  const res = await fetch('/api/rename', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: images[idx], new_label: newLabel })
  });
  const data = await res.json();
  if (data.ok) {
    images[idx] = data.new_path;
    toast('Renamed → ' + newLabel.toUpperCase());
  }
}

function prev() { if (images.length) { idx = (idx - 1 + images.length) % images.length; mode = 'nav'; typeBuf = ''; render(); } }
function next() { if (images.length) { idx = (idx + 1) % images.length; mode = 'nav'; typeBuf = ''; render(); } }

function jumpTo(query) {
  const q = query.toUpperCase();
  const i = images.findIndex(p => {
    const name = p.split('/').pop();
    const lbl = name.substring(0, name.lastIndexOf('.')).toUpperCase();
    return lbl === q || lbl.includes(q);
  });
  if (i >= 0) {
    idx = i;
    toast('Jumped to ' + q);
  } else {
    toast('Not found: ' + q);
  }
}

$('#btn-prev').addEventListener('click', prev);
$('#btn-next').addEventListener('click', next);
$('#folder-select').addEventListener('change', e => { if (e.target.value) loadImages(e.target.value); });

document.addEventListener('keydown', async e => {
  if (e.target.tagName === 'SELECT') return;

  // Tab toggles rename mode
  if (e.key === 'Tab') {
    e.preventDefault();
    if (mode === 'edit') { mode = 'nav'; typeBuf = ''; }
    else { mode = 'edit'; typeBuf = ''; }
    render(); return;
  }

  if (mode === 'edit') {
    if (e.key === 'Escape') {
      mode = 'nav'; typeBuf = ''; render();
    } else if (e.key === 'Enter') {
      if (typeBuf) await doRename(typeBuf);
      mode = 'nav'; typeBuf = ''; render();
    } else if (e.key === 'Backspace') {
      typeBuf = typeBuf.slice(0, -1);
      render(); e.preventDefault();
    } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey) {
      typeBuf += e.key;
      render(); e.preventDefault();
    }
  } else if (mode === 'search') {
    if (e.key === 'Escape') {
      mode = 'nav'; typeBuf = ''; render();
    } else if (e.key === 'Enter') {
      if (typeBuf) jumpTo(typeBuf);
      mode = 'nav'; typeBuf = ''; render();
    } else if (e.key === 'Backspace') {
      typeBuf = typeBuf.slice(0, -1);
      render(); e.preventDefault();
    } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey) {
      typeBuf += e.key;
      render(); e.preventDefault();
    }
  } else {
    if (e.key === 'j' || e.key === 'ArrowLeft') { prev(); e.preventDefault(); }
    else if (e.key === 'k' || e.key === 'ArrowRight') { next(); e.preventDefault(); }
    else if (e.key === '/') { mode = 'search'; typeBuf = ''; render(); e.preventDefault(); }
    else if (e.key === 'r') {
      e.preventDefault();
      if (!images.length) return;
      const name = images[idx].split('/').pop();
      if (confirm('Delete ' + name + '?')) {
        const res = await fetch('/api/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: images[idx] })
        });
        const data = await res.json();
        if (data.ok) {
          images.splice(idx, 1);
          if (idx >= images.length) idx = Math.max(0, images.length - 1);
          toast('Deleted ' + name);
          render();
        }
      }
    }
  }
});

loadFolders();
</script>
</body>
</html>"""


@app.route("/")
def index():
    return HTML


@app.route("/api/folders")
def folders():
    dirs = []
    for root, subdirs, files in os.walk(BASE_DIR):
        # skip hidden dirs and venv
        subdirs[:] = [d for d in subdirs if not d.startswith(".") and d != ".venv" and d != "node_modules"]
        imgs = [f for f in files if f.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"))]
        if imgs:
            rel = os.path.relpath(root, BASE_DIR)
            dirs.append(rel)
    dirs.sort()
    return jsonify(dirs)


@app.route("/api/images")
def list_images():
    folder = request.args.get("folder", "")
    abs_folder = os.path.abspath(os.path.join(BASE_DIR, folder))
    if not abs_folder.startswith(BASE_DIR):
        return jsonify([])
    exts = ("*.jpg", "*.jpeg", "*.png", "*.gif", "*.bmp", "*.webp",
            "*.JPG", "*.JPEG", "*.PNG")
    files = []
    for ext in exts:
        files.extend(glob.glob(os.path.join(abs_folder, ext)))
    files = sorted(set(files))
    return jsonify([os.path.relpath(f, BASE_DIR) for f in files])


@app.route("/api/image")
def serve_image():
    path = request.args.get("path", "")
    abs_path = os.path.abspath(os.path.join(BASE_DIR, path))
    if not abs_path.startswith(BASE_DIR) or not os.path.isfile(abs_path):
        return "not found", 404
    return send_file(abs_path)


@app.route("/api/rename", methods=["POST"])
def rename_file():
    data = request.json
    old_path = os.path.abspath(os.path.join(BASE_DIR, data["path"]))
    if not old_path.startswith(BASE_DIR) or not os.path.isfile(old_path):
        return jsonify({"ok": False})
    new_label = data["new_label"].upper()
    ext = os.path.splitext(old_path)[1]
    new_path = os.path.join(os.path.dirname(old_path), new_label + ext)
    os.rename(old_path, new_path)
    return jsonify({"ok": True, "new_path": os.path.relpath(new_path, BASE_DIR)})


@app.route("/api/delete", methods=["POST"])
def delete_file():
    data = request.json
    abs_path = os.path.abspath(os.path.join(BASE_DIR, data["path"]))
    if not abs_path.startswith(BASE_DIR) or not os.path.isfile(abs_path):
        return jsonify({"ok": False})
    os.remove(abs_path)
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = 5050
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    print(f"Label verifier running at http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)
