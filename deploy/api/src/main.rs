use axum::{
    Json, Router,
    extract::{Request, State},
    http::{self, HeaderMap, HeaderValue, StatusCode, header},
    middleware::{self, Next},
    response::{Html, IntoResponse, Response},
    routing::{get, post},
};
use base64::{Engine, engine::general_purpose::STANDARD};
use image::ImageReader;
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;
use std::io::{Cursor, Write};
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Instant, SystemTime, UNIX_EPOCH};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::UnixStream;
use tower_http::cors::{Any, CorsLayer};
use zip::{ZipWriter, write::SimpleFileOptions};

const SOLVER_SOCKET: &str = "/tmp/captop-solver.sock";
const MAX_IMAGE_BYTES: usize = 256 * 1024;

#[derive(Deserialize)]
struct SolveRequest {
    image: String,
}

#[derive(Serialize)]
struct SolveResponse {
    text: String,
}

struct AppState {
    solver_socket: String,
    report_dir: String,
    auth_password: String,
    logs: Arc<Logs>,
}

#[tokio::main]
async fn main() {
    let logs = Arc::new(Logs::new());
    let state = Arc::new(AppState {
        solver_socket: SOLVER_SOCKET.to_string(),
        report_dir: std::env::var("REPORT_DIR").unwrap_or_else(|_| "reports".to_string()),
        auth_password: std::env::var("AUTH_PASSWORD").unwrap_or_else(|_| String::new()),
        logs,
    });
    state.logs.log("api started".into());
    if let Err(e) = std::fs::create_dir_all(&state.report_dir) {
        state.logs.log(format!("report dir unavailable: {e}"));
    }

    let report_routes = Router::new()
        .route("/report", get(status_page))
        .route("/malformed", get(download_malformed))
        .layer(middleware::from_fn_with_state(state.clone(), basic_auth));

    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods(Any)
        .allow_headers(Any)
        .allow_private_network(true);

    let app = Router::new()
        .route("/solve", post(solve))
        .route("/health", get(health))
        .merge(report_routes)
        .layer(cors)
        .with_state(state);

    let port = std::env::var("PORT")
        .unwrap_or_else(|_| "3000".to_string())
        .parse::<u16>()
        .unwrap();

    let addr = format!("0.0.0.0:{}", port);
    let listener = tokio::net::TcpListener::bind(&addr).await.unwrap();
    println!("API running at http://{}", addr);
    axum::serve(listener, app).await.unwrap();
}

async fn basic_auth(State(state): State<Arc<AppState>>, req: Request, next: Next) -> Response {
    if state.auth_password.is_empty() {
        return StatusCode::NOT_FOUND.into_response();
    }
    if authorized(&state.auth_password, req.headers()) {
        return next.run(req).await;
    }
    (
        StatusCode::UNAUTHORIZED,
        [(
            http::header::WWW_AUTHENTICATE,
            "Basic realm=\"captcha-api\"",
        )],
        "Authentication required",
    )
        .into_response()
}

fn authorized(password: &str, headers: &HeaderMap) -> bool {
    if password.is_empty() {
        return false;
    }
    let Some(auth) = headers
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
    else {
        return false;
    };
    let Some(creds) = auth.strip_prefix("Basic ") else {
        return false;
    };
    let Ok(decoded) = STANDARD.decode(creds.trim()) else {
        return false;
    };
    let Ok(creds) = String::from_utf8(decoded) else {
        return false;
    };
    creds
        .split_once(':')
        .map(|(_, pass)| pass == password)
        .unwrap_or(false)
}

fn valid_report_name(name: &str) -> bool {
    name.len() > 4
        && name
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'.')
        && !name.starts_with('.')
        && name.ends_with(".png")
}

async fn solve(
    State(state): State<Arc<AppState>>,
    Json(payload): Json<SolveRequest>,
) -> Result<Json<SolveResponse>, StatusCode> {
    let now = now_secs();
    state.logs.log_request(now);
    state.logs.total_requests.fetch_add(1, Ordering::Relaxed);
    let image_data = match STANDARD.decode(&payload.image) {
        Ok(d) => d,
        Err(_) => {
            state.logs.log("solve 400 bad base64".into());
            return Err(StatusCode::BAD_REQUEST);
        }
    };

    if image_data.len() > MAX_IMAGE_BYTES {
        state.logs.log(format!("solve 413 {}B", image_data.len()));
        return Err(StatusCode::PAYLOAD_TOO_LARGE);
    }

    let decode_ok = ImageReader::new(Cursor::new(&image_data))
        .with_guessed_format()
        .map(|r| r.decode().is_ok())
        .unwrap_or(false);
    if !decode_ok {
        let name = save_malformed_image(&state.report_dir, &image_data).await;
        state.logs.log(format!("solve 400 malformed; saved {name}"));
        return Err(StatusCode::BAD_REQUEST);
    }

    let mut stream = match UnixStream::connect(&state.solver_socket).await {
        Ok(s) => s,
        Err(_) => {
            state.logs.log("solve 503 solver unreachable".into());
            return Err(StatusCode::SERVICE_UNAVAILABLE);
        }
    };

    let len = image_data.len() as u32;
    stream
        .write_all(&len.to_le_bytes())
        .await
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    stream
        .write_all(&image_data)
        .await
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    stream
        .shutdown()
        .await
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;

    let mut text = String::new();
    stream.read_to_string(&mut text).await.map_err(|_| {
        state.logs.log("solve 500 solver io".into());
        StatusCode::INTERNAL_SERVER_ERROR
    })?;

    state.logs.log(format!("solve 200 ({} chars)", text.len()));
    Ok(Json(SolveResponse { text }))
}

// ponytail: same-millisecond collisions overwrite; fine for a diagnostic dir
async fn save_malformed_image(dir: &str, data: &[u8]) -> String {
    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    let name = format!("report_{millis}.png");
    if let Err(e) = tokio::fs::write(format!("{dir}/{name}"), data).await {
        eprintln!("Failed to save malformed image: {}", e);
    }
    name
}

async fn download_malformed(State(state): State<Arc<AppState>>) -> Result<Response, StatusCode> {
    let mut names: Vec<String> = Vec::new();
    if let Ok(mut dir) = tokio::fs::read_dir(&state.report_dir).await {
        while let Ok(Some(entry)) = dir.next_entry().await {
            let name = entry.file_name().to_string_lossy().into_owned();
            if valid_report_name(&name) {
                names.push(name);
            }
        }
    }
    names.sort();

    let buf = Cursor::new(Vec::new());
    let mut zip = ZipWriter::new(buf);
    let options = SimpleFileOptions::default().compression_method(zip::CompressionMethod::Bzip2);

    for name in &names {
        let data = tokio::fs::read(format!("{}/{}", state.report_dir, name))
            .await
            .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
        if let Err(_e) = zip.start_file(name.as_str(), options) {
            return Err(StatusCode::INTERNAL_SERVER_ERROR);
        }
        if zip.write_all(&data).is_err() {
            return Err(StatusCode::INTERNAL_SERVER_ERROR);
        }
    }
    let buf = match zip.finish() {
        Ok(buf) => buf,
        Err(_) => return Err(StatusCode::INTERNAL_SERVER_ERROR),
    };

    for name in &names {
        let _ = tokio::fs::remove_file(format!("{}/{}", state.report_dir, name)).await;
    }

    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    state
        .logs
        .log(format!("purged {} images into zip", names.len()));
    let mut resp = (StatusCode::OK, buf.into_inner()).into_response();
    resp.headers_mut().insert(
        http::header::CONTENT_TYPE,
        HeaderValue::from_static("application/zip"),
    );
    resp.headers_mut().insert(
        http::header::CONTENT_DISPOSITION,
        HeaderValue::from_str(&format!("attachment; filename=\"malformed-{stamp}.zip\"")).unwrap(),
    );
    Ok(resp)
}

async fn health() -> StatusCode {
    StatusCode::OK
}

const LOG_CAP: usize = 500;
const TIMELINE_DAYS: u64 = 30;
const TIMELINE_MAX_ENTRIES: usize = 20_000;

struct Logs {
    start: Instant,
    total_requests: AtomicU64,
    lines: std::sync::Mutex<VecDeque<String>>,
    times: std::sync::Mutex<VecDeque<u64>>,
}

impl Logs {
    fn new() -> Self {
        Self {
            start: Instant::now(),
            total_requests: AtomicU64::new(0),
            lines: std::sync::Mutex::new(VecDeque::new()),
            times: std::sync::Mutex::new(VecDeque::new()),
        }
    }

    fn log(&self, msg: String) {
        let s = self.start.elapsed().as_secs();
        let mut lines = self.lines.lock().unwrap();
        lines.push_back(format!("[+{s}s] {msg}"));
        while lines.len() > LOG_CAP {
            lines.pop_front();
        }
    }

    fn log_request(&self, now: u64) {
        let mut times = self.times.lock().unwrap();
        times.push_back(now);
        let cutoff = now_secs().saturating_sub(TIMELINE_DAYS * 86400 - 1);
        times.retain(|&t| t >= cutoff);
        while times.len() > TIMELINE_MAX_ENTRIES {
            times.pop_front();
        }
    }
}

fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

// 30 buckets of daily request counts, index 0 = today
fn daily_buckets(times: &std::sync::Mutex<VecDeque<u64>>, now: u64) -> [u64; 30] {
    let times = times.lock().unwrap();
    let mut buckets = [0u64; 30];
    for &t in times.iter() {
        let day = now.saturating_sub(t) / 86400;
        if let Some(c) = buckets.get_mut(day as usize) {
            *c += 1;
        }
    }
    buckets
}

fn fmt_uptime(secs: u64) -> String {
    format!(
        "{}d {:02}:{:02}:{:02}",
        secs / 86400,
        secs % 86400 / 3600,
        secs % 3600 / 60,
        secs % 60
    )
}

fn timeline_svg(buckets: &[u64; 30], max: u64) -> String {
    const W: f64 = 660.0;
    const X0: f64 = 46.0;
    const TOP: f64 = 10.0;
    const BOTTOM: f64 = 118.0;
    let bw = (W - X0 - 4.0) / buckets.len() as f64;
    let y = |v: u64| {
        BOTTOM
            - if max > 0 {
                (v as f64 / max as f64) * (BOTTOM - TOP)
            } else {
                0.0
            }
    };
    let mut out = String::from("<svg viewBox=\"0 0 660 130\" preserveAspectRatio=\"none\">");
    for v in [max, max.div_ceil(2), 0] {
        let g = y(v);
        out.push_str(&format!(
            "<line x1=\"{X0}\" x2=\"{W}\" y1=\"{g:.1}\" y2=\"{g:.1}\" class=\"grid\"/>"
        ));
        out.push_str(&format!(
            "<text class=\"ax\" x=\"0\" y=\"{:.1}\">{v}</text>",
            g + 3.0
        ));
    }
    if buckets[0] > 0 {
        out.push_str(&format!(
            "<text class=\"ax\" x=\"{:.1}\" y=\"{:.1}\" text-anchor=\"middle\">{}</text>",
            X0 + bw / 2.0,
            y(buckets[0]) - 4.0,
            buckets[0]
        ));
    }
    for (i, &c) in buckets.iter().enumerate() {
        if c == 0 {
            continue;
        }
        out.push_str(&format!(
            "<rect x=\"{:.1}\" y=\"{:.1}\" width=\"{:.1}\" height=\"{:.1}\" class=\"bar\"/>",
            X0 + i as f64 * bw + 1.5,
            y(c),
            bw - 3.0,
            BOTTOM - y(c)
        ));
    }
    out.push_str("</svg>");
    out
}

async fn status_page(State(state): State<Arc<AppState>>) -> Html<String> {
    let lines: Vec<String> = state
        .logs
        .lines
        .lock()
        .unwrap()
        .iter()
        .rev()
        .cloned()
        .collect();
    let buckets = daily_buckets(&state.logs.times, now_secs());
    let max = *buckets.iter().max().unwrap();
    let last_req = state.logs.times.lock().unwrap().back().copied();
    let timeline_caption = match last_req {
        Some(t) => format!(
            "requests per day &middot; last 30d &middot; peak {max} &middot; last request {}s ago",
            now_secs().saturating_sub(t)
        ),
        None => "no requests yet".to_string(),
    };
    let log_html = if lines.is_empty() {
        "<li class=\"dim\">nothing yet</li>".to_string()
    } else {
        lines
            .iter()
            .map(|l| format!("<li>{l}</li>"))
            .collect::<Vec<_>>()
            .join("")
    };

    Html(format!(
        r#"<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta http-equiv="refresh" content="5"><title>API log</title>
<style>
html, body {{ background: #000; color: #0f0; font: 13px/1.5 monospace; margin: 0; }}
body {{ padding: 2rem; }}
h1 {{ font-size: 1rem; font-weight: normal; margin: 0 0 1.5rem; }}
.kpis {{ display: flex; gap: 3rem; flex-wrap: wrap; margin-bottom: 1.5rem; }}
.kpi b {{ display: block; font-size: 1.6rem; font-weight: normal; }}
.kpi span {{ color: #555; font-size: .75rem; }}
svg {{ width: 100%; max-width: 800px; height: 130px; display: block; margin: .4rem 0 1rem; }}
svg .bar {{ fill: #3f3; }}
svg .grid {{ stroke: #1c1c1c; }}
svg .ax {{ fill: #555; font-size: 10px; }}
#log-title {{ color: #555; font-size: .75rem; font-weight: normal; margin: 1.6rem 0 0; }}
ul {{ list-style: none; padding: 0; margin: .6rem 0; max-width: 900px; }}
li {{ padding: 3px 0; border-bottom: 1px solid #111; }}
.dim {{ color: #333; }}
footer {{ color: #444; font-size: .75rem; margin-top: 1.8rem; }}
</style></head><body>
<h1>captcha-api log</h1>
<div class="kpis">
<div class="kpi"><b>{}</b><span>uptime</span></div>
<div class="kpi"><b>{}</b><span>total requests</span></div>
<div class="kpi"><b>{}</b><span>log lines kept</span></div>
</div>
<p id="log-title">{}</p>
{}
<p id="log-title">requests timeline &middot; one green bar = one day, last 30 days &middot; numbers = requests/day</p>
<ul>{}</ul>
<footer>last 500 events, newest first &middot; auto-refresh 5s</footer>
</body></html>"#,
        fmt_uptime(state.logs.start.elapsed().as_secs()),
        state.logs.total_requests.load(Ordering::Relaxed),
        lines.len(),
        timeline_caption,
        timeline_svg(&buckets, max),
        log_html,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn basic_header(user: &str, pass: &str) -> HeaderValue {
        HeaderValue::from_str(&format!(
            "Basic {}",
            STANDARD.encode(format!("{user}:{pass}"))
        ))
        .unwrap()
    }

    #[test]
    fn auth_requires_matching_password() {
        let mut headers = HeaderMap::new();
        assert!(!authorized("pw", &headers));
        headers.insert(header::AUTHORIZATION, basic_header("u", "pw"));
        assert!(authorized("pw", &headers));
        assert!(!authorized("p0", &headers));
        headers.insert(header::AUTHORIZATION, HeaderValue::from_static("Bearer x"));
        assert!(!authorized("pw", &headers));
        headers.insert(
            header::AUTHORIZATION,
            HeaderValue::from_static("Basic not-base64!"),
        );
        assert!(!authorized("pw", &headers));
    }

    #[test]
    fn auth_disabled_without_password() {
        let mut headers = HeaderMap::new();
        headers.insert(header::AUTHORIZATION, basic_header("u", "pw"));
        assert!(!authorized("", &headers));
    }

    #[test]
    fn report_name_blocks_traversal() {
        assert!(valid_report_name("report_12.png"));
        assert!(!valid_report_name(".png"));
        assert!(!valid_report_name("../etc.png"));
        assert!(!valid_report_name("etc%2fpasswd.png"));
        assert!(!valid_report_name("report.txt"));
    }

    #[test]
    fn log_ring_keeps_last_lines() {
        let logs = Logs::new();
        for i in 0..(LOG_CAP + 50) {
            logs.log(format!("event {i}"));
        }
        let lines: Vec<String> = logs.lines.lock().unwrap().iter().cloned().collect();
        assert_eq!(lines.len(), LOG_CAP);
        assert!(lines.last().unwrap().contains("event 549"));
        assert!(lines.first().unwrap().contains("event 50"));
    }

    #[test]
    fn timeline_buckets_count_per_day() {
        let now = now_secs();
        let logs = Logs::new();
        logs.log_request(now);
        logs.log_request(now);
        logs.log_request(now.saturating_sub(86400));
        logs.log_request(now.saturating_sub(35 * 86400));
        let b = daily_buckets(&logs.times, now);
        assert_eq!(b[0], 2);
        assert_eq!(b[1], 1);
        assert!(b[2..].iter().all(|&x| x == 0));
        assert_eq!(logs.times.lock().unwrap().len(), 3);
    }
}
