# Audit: Performance Verification on 0.1 vCPU Shared (Render)

## Executive Verdict
The codebase has seen substantial, intelligent optimization specifically targeted at sub-core CPU constraints (evidenced by commits `5c81f54`, `bc5f38f`, `ebee8e4`, `46905ff`, `98cb228`). However, **it is not yet fully optimized for a 0.1 vCPU shared environment on Render**.

While it achieves ~3.6 RPS in isolation, three critical bottlenecks and two architectural risks cause severe CPU throttling and latency spikes under real-world traffic.

---

## What the Commit History Reveals

The commit log shows active profiling and incremental optimizations against CPU contention:
- `5c81f54`: Pinned `SOLVER_SESSIONS=1` after measuring a drop from 3.6 to 2.7 RPS when 4 sessions contended on a sub-core slice.
- `bc5f38f`: Eliminated redundant image decoding in `api` (previously decoded twice; now `solver` decodes once and signals `?` on bad inputs).
- `ebee8e4`: Replaced `softmax(logits)` with raw argmax in CTC decoding, saving 1,850 float exponentiations and 50 heap vector allocations per solve.
- `98cb228`: Moved synchronous ONNX inference to `tokio::task::spawn_blocking` and moved preprocessing outside the session mutex.
- `46905ff`: Added snapshot caching to `/report/data` using an atomic `perm` counter, preventing continuous JSON serialization of 10k logs and 20k timestamps on 5-second dashboard polls.
- `707700a`: Offloaded `bzip2` zip generation to `spawn_blocking` so zipping up to 64 images would not stall the async reactor.
- `bcc0318`: Removed the 10s/30s solve timeout so requests queue indefinitely.

---

## The 0.1 vCPU Render Environment: Mechanics

On Render's shared tiers:
- CPU allocation is controlled by the Linux Completely Fair Scheduler (CFS) cgroup quotas: `cpu.cfs_quota_us = 10000` (10ms) per `cpu.cfs_period_us = 100000` (100ms).
- CPU time across **all threads** is accumulated. If 10 threads run CPU-bound work simultaneously for 1ms wall-clock time, all 10ms of quota are consumed, and the kernel freezes (throttles) the container for the remaining 99ms.
- Any multithreading on CPU-bound code in a 0.1 vCPU container accelerates CFS throttling.

---

## Critical Issues & Unoptimized Paths

### 1. ONNX Runtime Thread Pool Defaults to Host Logical Cores (Major CFS Killer)
- **Location**: `deploy/api/src/solver.rs:34-39`
- **Issue**: `Session::builder(&env)` does not configure `with_intra_threads(1)` or `with_inter_threads(1)`.
- **Impact**: ONNX Runtime inspects the host machine (often 16 to 64 cores on Render cloud hosts) and spawns a thread pool matching the host core count. During `session.run()`, matrix multiplications are parallelized across dozens of threads. On a 0.1 vCPU slice, multiple threads firing at once exhaust the 10ms quota within 1ms, resulting in severe CFS throttling, jitter, and cache thrashing.
- **Fix**:
```rust
let session = Session::builder(&env)
    .unwrap()
    .with_intra_threads(1)
    .unwrap()
    .with_inter_threads(1)
    .unwrap()
    .with_optimization_level(GraphOptimizationLevel::Level3)
    .unwrap()
    .commit_from_file(&model_path)
    .unwrap();
```

### 2. Wasteful Lanczos3 Resampling on Images That Are Already 200x40
- **Location**: `deploy/api/src/solver.rs:138`
- **Issue**:
```rust
let gray = img.to_luma8();
let resized = image::imageops::resize(&gray, 200, 40, image::imageops::FilterType::Lanczos3);
```
- **Analysis**: All 3,929 captcha samples in the dataset are natively `(200, 40)` pixels. `image::imageops::resize` does not fast-path equal dimensions; it builds an 8-tap sinc filter kernel (`Lanczos3`) and resamples every single pixel. This burns unnecessary CPU on every solve request.
- **Fix**:
```rust
let gray = img.to_luma8();
let resized = if gray.dimensions() == (200, 40) {
    gray
} else {
    image::imageops::resize(&gray, 200, 40, image::imageops::FilterType::Triangle)
};
```

### 3. Bzip2 Compression on Already-Compressed PNG Images Burns Heavy CPU
- **Location**: `deploy/api/src/main.rs:324`
- **Issue**:
```rust
let options = SimpleFileOptions::default().compression_method(zip::CompressionMethod::Bzip2);
```
- **Analysis**: `/malformed` bundles up to 64 images. The images saved to disk are already compressed `.png` files. Bzip2 is notoriously CPU-intensive (Burrows-Wheeler transform).
  - Measured benchmark on 64 captchas:
    - `Bzip2`: 190.7ms CPU time -> 227KB output (larger due to block headers!).
    - `Stored` (no re-compression): 5.3ms CPU time -> 208KB output.
  - On 0.1 vCPU, 190ms of CPU time translates to ~1.9 seconds of container execution, starving any concurrent solve requests.
- **Fix**: Use `zip::CompressionMethod::Stored` or `Deflated`.

### 4. Unbounded Queueing & Zombie Solves (Commit `bcc0318`)
- **Location**: `deploy/api/src/main.rs:206`
- **Issue**: The solve timeout was completely removed.
- **Impact**: At ~3.6 RPS, if 50 requests arrive in a burst, the 50th request waits ~14-18s. At 100 requests, wait time is ~28s.
  - Clients (browsers/scripts) will time out and disconnect after 10-15s.
  - However, in `solver.rs`, `handle_connection` spawns inference onto `tokio::task::spawn_blocking`. Tasks in `spawn_blocking` cannot be cancelled.
  - The solver continues executing forward passes for dead requests, wasting the 0.1 vCPU on work whose response will never be read, starving fresh requests.
- **Fix**: Add a concurrency limit layer (`tower::limit::ConcurrencyLimitLayer::new(10)`) or re-introduce a 15-20s timeout on `api` with client-disconnect detection.

### 5. Tokio Multi-Thread Runtime Overhead on Sub-Core Containers
- **Location**: `deploy/api/src/main.rs:42` and `deploy/api/src/solver.rs:17`
- **Issue**: Both binaries use `#[tokio::main]`, which spawns worker threads equal to host CPU cores (16-64).
- **Impact**: Running two separate processes (`api` and `solver`) with dozens of worker threads and blocking threads creates unnecessary thread stack allocation and OS scheduler context switching on a 0.1 vCPU allocation.
- **Fix**: Configure Tokio to use single-threaded workers:
```rust
#[tokio::main(flavor = "current_thread")]
```
Or set `tokio::runtime::Builder::new_multi_thread().worker_threads(1)`.

### 6. `preprocess` Runs Synchronously Inside Async Event Loop
- **Location**: `deploy/api/src/solver.rs:82`
- **Issue**: `preprocess(&image_data)` (JPEG/PNG decoding) runs inside `handle_connection` on the Tokio async task before entering `spawn_blocking`.
- **Impact**: Under burst, multiple image decodes execute concurrently on Tokio worker threads, driving up CFS quota consumption before requests reach the serialized inference stage.
- **Fix**: Move `preprocess` inside the `spawn_blocking` closure alongside inference, or bound incoming connections.

---

## Summary Scorecard

| Area | Status | Detail |
|---|---|---|
| Single ORT Session | Optimized | `SOLVER_SESSIONS=1` prevents multi-session contention. |
| Zero-Copy / Single Decode | Optimized | Image decoded once in solver; api pass-through. |
| CTC Decoding | Optimized | Argmax on raw logits; no `exp()` or heap vecs. |
| Report Page Polling | Optimized | Atomic `perm` cached JSON bytes. |
| ORT Internal Threading | **Unoptimized** | Defaults to host cores; needs `.with_intra_threads(1)`. |
| Image Resizing | **Unoptimized** | Runs Lanczos3 on 200x40 images that are already 200x40. |
| Zip Compression | **Unoptimized** | Bzip2 on PNGs wastes up to 2s of 0.1 vCPU time. |
| Backpressure & Queueing | **Unoptimized** | Infinite queue + uncancelable zombie solves. |
| Tokio Runtime Profile | **Sub-optimal** | Multi-threaded default on host cores for both binaries. |
