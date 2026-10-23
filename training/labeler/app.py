import os
import re
import time

from flask import Flask, jsonify, request, send_from_directory, render_template

app = Flask(__name__, template_folder='templates', static_folder='static')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(BASE_DIR))
DATA_DIR = os.environ.get(
    "EDITABLE_CAPTCHAS", os.path.join(ROOT, "training", "data", "malformed")
)
LABELED_DIR = os.environ.get(
    "LABELED_CAPTCHAS", os.path.join(ROOT, "training", "data", "captchas")
)
LABEL_RE = re.compile(r"^[0-9a-z]{6}$")

FILE_EXTS = (".png", ".jpg", ".jpeg")


def durable_rename(src, dst):
    """Atomic move that survives power loss: fsync both dirs' metadata."""
    os.replace(src, dst)
    for d in (os.path.dirname(src), os.path.dirname(dst)):
        fd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

# in-memory: original malformed name -> label it was saved as in captchas
HISTORY = {}


def scan_files():
    return sorted(
        f for f in os.listdir(DATA_DIR)
        if f.lower().endswith(FILE_EXTS)
    )


def ext_of(name):
    return os.path.splitext(name)[1]


def find_file(name):
    """Resolve a name to an exact or extension-unknown filename, else None."""
    if os.path.isfile(os.path.join(DATA_DIR, name)):
        return name
    for ext in FILE_EXTS:
        candidate = name + ext
        if os.path.isfile(os.path.join(DATA_DIR, candidate)):
            return candidate
    return None


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/files')
def files():
    return jsonify({"files": scan_files(), "labeled": HISTORY})


@app.route('/api/rename', methods=['POST'])
def rename():
    data = request.get_json(silent=True) or {}
    label = str(data.get("label", "")).strip().lower()
    name = str(data.get("name", ""))
    if not LABEL_RE.match(label):
        return jsonify({"error": f"invalid label {label!r}: need 6 chars [0-9a-z]"}), 400
    src_name = find_file(name)
    if src_name:  # fresh: move malformed/<src> -> captchas/<label>
        dst = os.path.join(LABELED_DIR, label + ext_of(src_name))
        if os.path.exists(dst):
            # same-label conflict: keep this copy for review, park it as unixtime
            parked = f"{int(time.time() * 1000)}{ext_of(src_name)}"
            durable_rename(os.path.join(DATA_DIR, src_name), os.path.join(DATA_DIR, parked))
            HISTORY[src_name] = parked
            return jsonify({"gone": True})
        os.makedirs(LABELED_DIR, exist_ok=True)
        durable_rename(os.path.join(DATA_DIR, src_name), dst)
        HISTORY[src_name] = os.path.basename(dst)
        return jsonify({"gone": True})
    if name in HISTORY:  # already labeled: correct captchas/<old> -> <label>
        old = HISTORY[name]
        ext = ext_of(old)
        new = os.path.join(LABELED_DIR, label + ext)
        if os.path.basename(new) == old:
            return jsonify({"gone": True})
        if os.path.exists(new):
            return jsonify({"error": f"{new} already exists - label collision"}), 400
        durable_rename(os.path.join(LABELED_DIR, old), new)
        HISTORY[name] = os.path.basename(new)
        return jsonify({"gone": True})
    return jsonify({"error": "file not found (already moved or stale view)"}), 404


@app.route('/labelled/<filename>')
def serve_labeled(filename):
    if os.path.isfile(os.path.join(LABELED_DIR, filename)):
        return send_from_directory(LABELED_DIR, filename)
    return send_from_directory(DATA_DIR, filename)  # parked conflict copies


@app.route('/api/delete', methods=['POST'])
def delete():
    data = request.get_json(silent=True) or {}
    src_name = find_file(str(data.get("name", "")))
    if not src_name:
        return jsonify({"error": f"{src_name} not found"}), 404
    os.remove(os.path.join(DATA_DIR, src_name))
    return jsonify({"deleted": src_name})


@app.route('/captchas/<filename>')
def serve_captcha(filename):
    return send_from_directory(DATA_DIR, filename)


if __name__ == "__main__":
    app.run(debug=False, port=int(os.environ.get("LABELER_PORT", 5000)))
