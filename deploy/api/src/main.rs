use axum::{
    Json, Router,
    extract::{DefaultBodyLimit, Request, State},
    http::{self, HeaderMap, HeaderValue, StatusCode, header},
    body::Bytes,
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use base64::{Engine, engine::general_purpose::STANDARD};
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;
use std::io::{Cursor, Write};
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
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

    // report page + its data are public; only the malformed-zip purge needs a password
    let report_routes = Router::new()
        .route("/report", get(report_page))
        .route("/report.css", get(report_css))
        .route("/report.js", get(report_js))
        .route("/report/data", get(report_data));

    let malformed_routes = Router::new()
        .route("/malformed", get(download_malformed))
        .layer(middleware::from_fn_with_state(state.clone(), basic_auth));

    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods(Any)
        .allow_headers(Any)
        .allow_private_network(true);

    let app = Router::new()
        // base64 of the 256KB image max is ~350KB; 2MB axum default invites floods
        .route("/solve", post(solve).layer(DefaultBodyLimit::max(512 * 1024)))
        .route("/health", get(health))
        .merge(report_routes)
        .merge(malformed_routes)
        .layer(cors)
        .with_state(state);

    let port: u16 = std::env::var("PORT").unwrap_or_else(|_| "3000".into()).parse().unwrap();

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
        .is_some_and(|(_, pass)| ct_eq(pass, password))
}

// same 64 iterations always, so response timing can't leak password bytes;
// ponytail: compares only the first 64 bytes — passwords that long truncate-match
fn ct_eq(a: &str, b: &str) -> bool {
    let (a, b) = (a.as_bytes(), b.as_bytes());
    let mut d = (a.len() != b.len()) as u8;
    for i in 0..64 {
        d |= a.get(i).copied().unwrap_or(0) ^ b.get(i).copied().unwrap_or(0);
    }
    d == 0
}

fn valid_report_name(name: &str) -> bool {
    name.len() > 4
        && name
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'.')
        && !name.starts_with('.')
        && name.ends_with(".png")
}

// every failure path: accounting + log + status in one place
fn deny(logs: &Logs, msg: impl std::fmt::Display, code: StatusCode) -> StatusCode {
    logs.served_failure();
    logs.log(msg.to_string());
    code
}

async fn solve(
    State(state): State<Arc<AppState>>,
    Json(payload): Json<SolveRequest>,
) -> Result<Json<SolveResponse>, StatusCode> {
    let now = now_secs();
    state.logs.log_request(now);
    let image_data = match STANDARD.decode(&payload.image) {
        Ok(d) => d,
        Err(_) => {
            return Err(deny(&state.logs, "solve 400 bad base64", StatusCode::BAD_REQUEST));
        }
    };

    if image_data.len() > MAX_IMAGE_BYTES {
        return Err(deny(
            &state.logs,
            format!("solve 413 {}B", image_data.len()),
            StatusCode::PAYLOAD_TOO_LARGE,
        ));
    }

    // a hung solver used to wedge the task forever; the whole exchange is bounded
    let io = async {
        let mut stream = UnixStream::connect(&state.solver_socket)
            .await
            .map_err(|_| deny(&state.logs, "solve 503 solver unreachable", StatusCode::SERVICE_UNAVAILABLE))?;

        let len = image_data.len() as u32;
        stream.write_all(&len.to_le_bytes()).await.map_err(|_| {
            deny(&state.logs, "solve 500 solver write", StatusCode::INTERNAL_SERVER_ERROR)
        })?;
        stream.write_all(&image_data).await.map_err(|_| {
            deny(&state.logs, "solve 500 solver write", StatusCode::INTERNAL_SERVER_ERROR)
        })?;
        stream.shutdown().await.map_err(|_| {
            deny(&state.logs, "solve 500 solver write", StatusCode::INTERNAL_SERVER_ERROR)
        })?;

        let mut text = String::new();
        stream
            .read_to_string(&mut text)
            .await
            .map(|_| text)
            .map_err(|_| deny(&state.logs, "solve 500 solver io", StatusCode::INTERNAL_SERVER_ERROR))
    };
    let text = match tokio::time::timeout(Duration::from_secs(10), io).await {
        Ok(r) => r?,
        Err(_) => {
            return Err(deny(&state.logs, "solve 503 solver timeout", StatusCode::SERVICE_UNAVAILABLE));
        }
    };

    // solver marks internal failures with "!"; EOF can no longer fake an
    // empty-success decode
    if text == "!" {
        return Err(deny(&state.logs, "solve 500 solver err", StatusCode::INTERNAL_SERVER_ERROR));
    }

    // solver marks undecodable input with "?" (outside its output alphabet)
    if text == "?" {
        let line = match save_malformed_image(&state.report_dir, &image_data).await {
            Some(n) => format!("solve 400 malformed; saved {n}"),
            None => "solve 400 malformed; report dir full".into(),
        };
        return Err(deny(&state.logs, line, StatusCode::BAD_REQUEST));
    }

    state.logs.served.fetch_add(1, Ordering::Relaxed);
    state.logs.log(format!("solve 200 ({} chars)", text.len()));
    Ok(Json(SolveResponse { text }))
}

// flood floor: a public /solve can drop unbounded files otherwise; the purge
// then zips the whole dir into memory. Both sides are capped instead.
const MAX_MALFORMED_FILES: usize = 500;
const MAX_ZIP_ENTRIES: usize = 64;

async fn list_malformed(dir: &str) -> Vec<String> {
    let mut names: Vec<String> = Vec::new();
    if let Ok(mut dir) = tokio::fs::read_dir(dir).await {
        while let Ok(Some(entry)) = dir.next_entry().await {
            let name = entry.file_name().to_string_lossy().into_owned();
            if valid_report_name(&name) {
                names.push(name);
            }
        }
    }
    names.sort();
    names
}

// ponytail: same-millisecond collisions overwrite; fine for a diagnostic dir.
// write .part + rename so a concurrent purge can never zip a half-written file
// (dot-names are invisible to valid_report_name); None = skipped (cap or io)
async fn save_malformed_image(dir: &str, data: &[u8]) -> Option<String> {
    if list_malformed(dir).await.len() >= MAX_MALFORMED_FILES {
        return None;
    }
    let millis = now_millis();
    let name = format!("report_{millis}.png");
    let tmp = format!("{dir}/.{name}.part");
    let path = format!("{dir}/{name}");
    if tokio::fs::write(&tmp, data).await.is_ok()
        && tokio::fs::rename(&tmp, &path).await.is_ok()
    {
        return Some(name);
    }
    let _ = tokio::fs::remove_file(&tmp).await;
    eprintln!("Failed to save malformed image '{name}'");
    None
}

async fn download_malformed(State(state): State<Arc<AppState>>) -> Result<Response, StatusCode> {
    // zip or io failure in here is opaque to the caller; same 500 everywhere
    fn e500() -> StatusCode {
        StatusCode::INTERNAL_SERVER_ERROR
    }
    let mut names = list_malformed(&state.report_dir).await;
    let start = names.len().saturating_sub(MAX_ZIP_ENTRIES);
    let names = names.split_off(start);
    let total_files = start + names.len();

    // files off the wire first; zipping up to 64×256KB bzip2 is seconds of CPU,
    // sync work used to stall a tokio worker mid-handler
    let mut files = Vec::with_capacity(names.len());
    for name in &names {
        let data = tokio::fs::read(format!("{}/{}", state.report_dir, name))
            .await
            .map_err(|_| e500())?;
        files.push((name.clone(), data));
    }
    let buf = tokio::task::spawn_blocking(move || build_zip(files))
        .await
        .map_err(|_| e500())?
        .map_err(|_| e500())?;

    for name in &names {
        let _ = tokio::fs::remove_file(format!("{}/{}", state.report_dir, name)).await;
    }

    let stamp = now_millis();
    state
        .logs
        .log(format!("purged {} images into zip", names.len()));
    let mut resp = (StatusCode::OK, buf).into_response();
    resp.headers_mut().insert(
        http::header::CONTENT_TYPE,
        HeaderValue::from_static("application/zip"),
    );
    resp.headers_mut().insert(
        http::header::CONTENT_DISPOSITION,
        HeaderValue::from_str(&format!(
            "attachment; filename=\"malformed-{stamp}.zip\""
        ))
        .unwrap(),
    );
    if total_files > MAX_ZIP_ENTRIES {
        state.logs.log(format!(
            "{} malformed images remain on disk (zip capped at {MAX_ZIP_ENTRIES}; dir capped at {MAX_MALFORMED_FILES})",
            total_files - names.len()
        ));
    }
    Ok(resp)
}

// sync zip build for spawn_blocking
fn build_zip(files: Vec<(String, Vec<u8>)>) -> Result<Vec<u8>, Box<dyn std::error::Error + Send + Sync>> {
    let mut zip = ZipWriter::new(Cursor::new(Vec::new()));
    let options = SimpleFileOptions::default().compression_method(zip::CompressionMethod::Bzip2);
    for (name, data) in &files {
        zip.start_file(name.as_str(), options)?;
        zip.write_all(data)?;
    }
    Ok(zip.finish()?.into_inner())
}

async fn health(State(state): State<Arc<AppState>>) -> StatusCode {
    match UnixStream::connect(&state.solver_socket).await {
        Ok(_) => StatusCode::OK,
        Err(_) => StatusCode::SERVICE_UNAVAILABLE,
    }
}

const LOG_CAP: usize = 10_000;
const TIMELINE_MAX_ENTRIES: usize = 20_000;

struct Logs {
    start: Instant,
    served: AtomicU64,
    failed: AtomicU64,
    lines: std::sync::Mutex<VecDeque<(u64, String)>>,
    times: std::sync::Mutex<VecDeque<u64>>,
    perm: AtomicU64,
    cache: std::sync::Mutex<Option<(u64, Bytes, Bytes)>>,
}

impl Logs {
    fn new() -> Self {
        Self {
            start: Instant::now(),
            served: AtomicU64::new(0),
            failed: AtomicU64::new(0),
            lines: std::sync::Mutex::new(VecDeque::new()),
            times: std::sync::Mutex::new(VecDeque::new()),
            perm: AtomicU64::new(0),
            cache: std::sync::Mutex::new(None),
        }
    }

    fn log(&self, msg: String) {
        let mut lines = self.lines.lock().unwrap();
        lines.push_back((now_secs(), msg));
        while lines.len() > LOG_CAP {
            lines.pop_front();
        }
        drop(lines);
        self.perm.fetch_add(1, Ordering::Relaxed);
    }

    fn served_failure(&self) {
        self.failed.fetch_add(1, Ordering::Relaxed);
    }

    fn log_request(&self, now: u64) {
        let mut times = self.times.lock().unwrap();
        times.push_back(now);
        while times.len() > TIMELINE_MAX_ENTRIES {
            times.pop_front();
        }
        drop(times);
        self.perm.fetch_add(1, Ordering::Relaxed);
    }

    // public dashboards poll /report/data every 5s; serializing 10k logs +
    // 20k stamps per poll was the cheapest public amplification. Cached arrays
    // invalidated by a perm counter instead.
    fn snapshot(&self) -> (Bytes, Bytes) {
        let perm = self.perm.load(Ordering::Relaxed);
        if let Some((p, logs, times)) = self.cache.lock().unwrap().as_ref()
            && *p == perm
        {
            return (logs.clone(), times.clone());
        }
        let logs: Bytes = serde_json::to_vec(&*self.lines.lock().unwrap()).unwrap().into();
        let times: Bytes = serde_json::to_vec(&*self.times.lock().unwrap()).unwrap().into();
        *self.cache.lock().unwrap() = Some((perm, logs.clone(), times.clone()));
        (logs, times)
    }
}

fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn now_millis() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0)
}

const CACHE: &str = "public, max-age=300";

// report page + assets are compile-time statics; only its data is live
fn asset(ct: &'static str, body: &'static str) -> impl IntoResponse {
    ([(header::CONTENT_TYPE, ct), (header::CACHE_CONTROL, CACHE)], body)
}

async fn report_page() -> impl IntoResponse {
    asset("text/html; charset=utf-8", include_str!("report.html"))
}

async fn report_css() -> impl IntoResponse {
    asset("text/css", include_str!("report.css"))
}

async fn report_js() -> impl IntoResponse {
    asset("application/javascript", include_str!("report.js"))
}

async fn report_data(State(state): State<Arc<AppState>>) -> Response {
    // heavy arrays come from the cache; KPIs stay fresh per call
    let (logs, times) = state.logs.snapshot();
    let mut body = format!(
        "{{\"now\":{now},\"uptime\":{uptime},\"served\":{served},\"failed\":{failed},\"logs\":",
        now = now_secs(),
        uptime = state.logs.start.elapsed().as_secs(),
        served = state.logs.served.load(Ordering::Relaxed),
        failed = state.logs.failed.load(Ordering::Relaxed),
    )
    .into_bytes();
    body.extend_from_slice(&logs);
    body.extend_from_slice(b",\"times\":");
    body.extend_from_slice(&times);
    body.push(b'}');
    (
        StatusCode::OK,
        [(header::CONTENT_TYPE, "application/json")],
        body,
    )
        .into_response()
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
    fn ct_eq_true_only_for_equal() {
        assert!(ct_eq("pw", "pw"));
        assert!(!ct_eq("pw", "pW"));
        assert!(!ct_eq("pw", "pw1"));
        assert!(!ct_eq("pw", "p"));
        assert!(!ct_eq("p", "pw"));
        // prefix match + enough padding bytes to wrap the old u8 len-fold
        let px = "pw".to_string() + &"\0".repeat(63) + &"x".repeat(256);
        assert!(!ct_eq("pw", &px));
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
        let lines: Vec<String> = logs
            .lines
            .lock()
            .unwrap()
            .iter()
            .map(|(_, m)| m.clone())
            .collect();
        assert_eq!(lines.len(), LOG_CAP);
        assert!(lines.last().unwrap().contains(&format!("event {}", LOG_CAP + 49)));
        assert!(lines.first().unwrap().contains("event 50"));
        assert!(lines.first().unwrap().contains("event 49") == false);
    }

    #[test]
    fn snapshot_tracks_logs_and_caches() {
        let logs = Logs::new();
        let empty = serde_json::from_slice::<serde_json::Value>(&logs.snapshot().0).unwrap();
        assert!(empty.as_array().unwrap().is_empty());
        logs.log("x".into());
        logs.log_request(now_secs());
        let (arr, times) = logs.snapshot();
        assert_eq!(
            serde_json::from_slice::<serde_json::Value>(&arr)
                .unwrap()
                .as_array()
                .unwrap()
                .len(),
            1
        );
        assert_eq!(
            serde_json::from_slice::<serde_json::Value>(&times)
                .unwrap()
                .as_array()
                .unwrap()
                .len(),
            1
        );
        // no new events: cache returns identical bytes
        let (arr2, times2) = logs.snapshot();
        assert_eq!(arr, arr2);
        assert_eq!(times, times2);
    }

}
