#!/usr/bin/env python3
"""Local single-human labeler.

Spawns a local web server that shows each unlabeled captcha in
training/data/malformed and moves it, renamed to its typed label, into
training/data/captchas as soon as the label is submitted. When malformed
is empty the page prints the push + model-refresh commands.

Run: python training/labeler/local.py   (then open http://localhost:8765)
"""
import os
import re
import sys
import urllib.parse
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MALFORMED = os.path.join(ROOT, "training", "data", "malformed")
CAPTCHAS = os.path.join(ROOT, "training", "data", "captchas")
PORT = int(os.environ.get("LABELER_PORT", 8765))
LABEL_RE = re.compile(r"^[0-9a-z]{6}$")

PAGE = """<!doctype html><title>local labeler</title>
<style>
 body{font-family:sans-serif;max-width:420px;margin:2rem auto}
 img{display:block;max-width:100%;border:1px solid #ccc;margin:.6rem 0}
 .err{color:#b00}.done{color:#060}
 input{width:12ch;font-size:1.2rem}
 code{background:#eee;padding:2px 4px}
</style><h1>local labeler</h1>
{body}
</html>"""


def remaining():
    return sorted(
        f for f in os.listdir(MALFORMED)
        if f.lower().endswith((".png", ".jpg", ".jpeg"))
    )


def move_label(name, label):
    """Rename malformed/<name> to captchas/<label><ext>. Returns error text or None."""
    label = label.lower().strip()
    if not LABEL_RE.match(label):
        return f"invalid label {label!r}: need exactly 6 chars of [0-9a-z]"
    src = os.path.join(MALFORMED, name)
    if not os.path.isfile(src):
        return f"missing file {name}"
    ext = os.path.splitext(name)[1]
    dst = os.path.join(CAPTCHAS, label + ext)
    if os.path.exists(dst):
        return f"{dst} already exists - label collision, enter a corrected label"
    os.rename(src, dst)
    return None


def next_image_query():
    remaining_files = remaining()
    if not remaining_files:
        push = (
            f"git add -A training/data && git commit -m labeled-{date.today():%F} && git push\n"
            "gh workflow run model-refresh -f min_accuracy=40"
        )
        return (
            '<p class="done">malformed is empty - dataset is labeled!</p>'
            f"<h2>redeploy</h2><pre>{push}</pre>"
        )
    name = remaining_files[0]
    return (
        f"<p>{len(remaining_files)} left</p>"
        f'<img src="/img/{urllib.parse.quote(name)}" alt="captcha">'
        '<form method="post" action="/label">'
        f'<input type="hidden" name="file" value="{name}">'
        '<input name="label" placeholder="6 chars" autofocus autocomplete="off"> '
        "<button>save</button></form>"
    )


class Handler(BaseHTTPRequestHandler):
    def _page(self, html, status=200):
        body = PAGE.replace("{body}", html).encode()
        self.send_response(status)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/":
            err = urllib.parse.parse_qs(url.query).get("err", [""])[0]
            img_html = f'<p class="err">{err}</p>' if err else ""
            self._page(img_html + next_image_query())
        elif self.path.startswith("/img/"):
            name = urllib.parse.unquote(self.path[len("/img/"):])
            path = os.path.normpath(os.path.join(MALFORMED, name))
            ext = os.path.splitext(path)[1].lower()
            mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}
            ctype = mime.get(ext.lstrip("."), "")
            try:
                if not ctype or not path.startswith(MALFORMED):
                    raise OSError
                with open(path, "rb") as f:
                    data = f.read()
            except OSError:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("content-type", ctype)
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path != "/label":
            self.send_error(404)
            return
        length = int(self.headers.get("content-length", 0))
        form = urllib.parse.parse_qs(self.rfile.read(length).decode())
        err = move_label(form.get("file", [""])[0], form.get("label", [""])[0])
        loc = "/" + (f"?err={urllib.parse.quote(err, safe='')}" if err else "")
        self.send_response(303)
        self.send_header("location", loc)
        self.end_headers()

    def log_message(self, *args):
        pass


def main():
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


def selftest():
    import shutil
    import tempfile
    import threading
    import urllib.request

    global MALFORMED, CAPTCHAS, PORT
    tmp = tempfile.mkdtemp()
    MALFORMED = os.path.join(tmp, "malformed")
    CAPTCHAS = os.path.join(tmp, "captchas")
    os.makedirs(MALFORMED)
    os.makedirs(CAPTCHAS)
    with open(os.path.join(MALFORMED, "23x45z.png"), "wb") as f:
        f.write(b"png")
    PORT = 18765
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    base = f"http://127.0.0.1:{PORT}"
    home = urllib.request.urlopen(base + "/").read().decode()
    assert "23x45z.png" in home and "1 left" in home, home

    def post(file, label):
        data = urllib.parse.urlencode({"file": file, "label": label}).encode()
        return urllib.request.urlopen(base + "/label", data=data)

    post("23x45z.png", "ABC45Z")  # upper case must be normalized
    assert os.path.isfile(os.path.join(CAPTCHAS, "abc45z.png")), "move failed"
    assert not os.listdir(MALFORMED), "malformed not drained"

    home = urllib.request.urlopen(base + "/").read().decode()
    assert "malformed is empty" in home and "model-refresh" in home, home

    # collision and invalid label must surface errors and move nothing
    with open(os.path.join(MALFORMED, "99aaaa.png"), "wb") as f:
        f.write(b"png")
    r = post("99aaaa.png", "abc12")  # 303 to ?err=invalid... (short label)
    home = urllib.request.urlopen(r.geturl()).read().decode()
    assert "invalid label" in home, home
    r = post("99aaaa.png", "abc45z")  # 303 to ?err=already exists (collision)
    home = urllib.request.urlopen(r.geturl()).read().decode()
    assert "already exists" in home, home
    assert os.path.isfile(os.path.join(MALFORMED, "99aaaa.png")), "bad label moved a file"

    shutil.rmtree(tmp)
    srv.shutdown()
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
