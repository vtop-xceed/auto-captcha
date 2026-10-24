use image::ImageReader;
use ndarray::Array4;
use ort::session::{Session, builder::GraphOptimizationLevel};
use ort::value::Tensor;
use std::io::Cursor;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::UnixListener;

const CHARS: &str = "0123456789abcdefghijklmnopqrstuvwxyz";
const SOCKET_PATH: &str = "/tmp/captop-solver.sock";
// mirrors the api's own cap before it writes the prefix; a foreign/local
// process claiming u32::MAX would otherwise allocate 4GB on first read
const MAX_IMAGE_BYTES: u32 = 256 * 1024;

#[tokio::main]
async fn main() {
    // Clean up stale socket
    let _ = std::fs::remove_file(SOCKET_PATH);

    let model_path =
        std::env::var("MODEL_PATH").unwrap_or_else(|_| "../model/captcha_model.onnx".into());

    let env = ort::init().build().unwrap();
    // ponytail: round-robin pool, no backpressure; SOLVER_SESSIONS if contention
    // ever shows up in the metrics. .max(1): SOLVER_SESSIONS=0 would modulo-panic
    // on the first request
    let pool_size: usize = std::env::var("SOLVER_SESSIONS")
        .ok().and_then(|s| s.parse().ok()).unwrap_or(4)
        .max(1);
    let sessions: Vec<_> = (0..pool_size)
        .map(|_| {
            let session = Session::builder(&env)
                .unwrap()
                // ponytail: 1 thread per session; default scales to host cores,
                // which explodes Linux CFS quota on a 0.1 vCPU shared slice
                .with_intra_threads(1)
                .unwrap()
                .with_inter_threads(1)
                .unwrap()
                .with_optimization_level(GraphOptimizationLevel::Level3)
                .unwrap()
                .commit_from_file(&model_path)
                .unwrap();
            Arc::new(Mutex::new(session))
        })
        .collect();
    let next = AtomicUsize::new(0);

    let listener = UnixListener::bind(SOCKET_PATH).unwrap();
    println!(
        "Solver listening on {} ({} sessions)",
        SOCKET_PATH, pool_size
    );

    loop {
        // a transient accept failure (fd exhaustion…) must not kill the solver;
        // unwrap here turned one bad connection into a full outage
        let Ok((stream, _)) = listener.accept().await else {
            eprintln!("accept failed, retrying");
            tokio::time::sleep(std::time::Duration::from_secs(1)).await;
            continue;
        };
        let i = next.fetch_add(1, Ordering::Relaxed) % sessions.len();
        let session = Arc::clone(&sessions[i]);

        tokio::spawn(async move {
            if let Err(e) = handle_connection(stream, &session).await {
                eprintln!("Solver error: {}", e);
            }
        });
    }
}

async fn handle_connection(
    mut stream: tokio::net::UnixStream,
    session: &Arc<Mutex<Session>>,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let len = stream.read_u32_le().await?; // LE u32, matches api's prefix write
    if len > MAX_IMAGE_BYTES {
        return Err(format!("image too large: {len}B").into());
    }

    let mut image_data = vec![0u8; len as usize];
    stream.read_exact(&mut image_data).await?;

    let session = session.clone();
    let text = tokio::task::spawn_blocking(move || match preprocess(&image_data) {
        Ok(tensor) => {
            // "!" marks internal failure (api answers 500), "?" marks
            // undecodable input (api answers 400) — neither fakes a result
            run_inference(tensor, session).unwrap_or_else(|e| {
                eprintln!("Solver inference error: {e}");
                "!".into()
            })
        }
        // api can't tell EOF-errors from a legit empty CTC decode, so decode
        // failures get a marker character instead of silence ("?" is outside
        // the [0-9A-Z] output alphabet)
        Err(_) => "?".into(),
    })
    // a panic inside preprocess/run_inference must also become "!", not an early
    // return — the open stream would EOF and the api would serve a fake
    // empty-captcha 200
    .await
    .unwrap_or_else(|e| {
        eprintln!("Solver task error: {e}");
        "!".into()
    });

    stream.write_all(text.as_bytes()).await?;
    stream.shutdown().await?;

    Ok(())
}

fn run_inference(
    tensor: Array4<f32>,
    session: Arc<Mutex<Session>>,
) -> Result<String, Box<dyn std::error::Error + Send + Sync>> {
    let input = Tensor::from_array(tensor)?;
    // poison-steal: a panicking run must not brick this slot forever
    let mut session = match session.lock() {
        Ok(g) => g,
        Err(p) => p.into_inner(),
    };
    let outputs = session.run(ort::inputs!["image" => input])?;
    let (_, raw) = outputs["output"].try_extract_tensor::<f32>()?;
    Ok(ctc_decode(raw))
}

fn preprocess(data: &[u8]) -> Result<Array4<f32>, Box<dyn std::error::Error + Send + Sync>> {
    let img = ImageReader::new(Cursor::new(data))
        .with_guessed_format()?
        .decode()?;

    let gray = img.to_luma8();
    // ponytail: VTOP captchas are natively 200x40; skip the heavy Lanczos3 sinc
    // filter unless an unexpected aspect ratio actually needs resampling
    let resized = if gray.dimensions() == (200, 40) {
        gray
    } else {
        image::imageops::resize(&gray, 200, 40, image::imageops::FilterType::Triangle)
    };

    let normalized: Vec<f32> = resized
        .iter()
        .map(|&p| (p as f32 / 255.0 - 0.5) / 0.5)
        .collect();

    Ok(Array4::from_shape_vec((1, 1, 40, 200), normalized)?)
}

fn ctc_decode(output: &[f32]) -> String {
    let vocab_size = 37;
    let time_steps = 50;
    let mut result = String::new();
    let mut prev_idx = 0usize;

    for t in 0..time_steps {
        let start = t * vocab_size;
        let logits = &output[start..start + vocab_size];

        // argmax(logits) == argmax(softmax(logits)) — no exp needed
        let idx = logits
            .iter()
            .copied()
            .enumerate()
            .max_by(|a, b| a.1.total_cmp(&b.1))
            .unwrap()
            .0;

        if idx != 0
            && idx != prev_idx
            && let Some(c) = CHARS.chars().nth(idx - 1)
        {
            result.push(c);
        }
        prev_idx = idx;
    }

    result.to_uppercase()
}

#[cfg(test)]
mod tests {
    use super::*;

    const VOCAB: usize = 37;

    fn output_with(steps: &[(usize, usize)]) -> Vec<f32> {
        let mut output = vec![0f32; VOCAB * 50];
        for t in 0..50 {
            output[t * VOCAB] = 10.0;
        }
        for &(t, idx) in steps {
            output[t * VOCAB + idx] = 20.0;
        }
        output
    }

    #[test]
    fn ctc_decode_dedupes_and_skips_blank() {
        let output = output_with(&[(0, 1), (1, 1), (2, 0), (3, 2), (4, 1)]);
        assert_eq!(ctc_decode(&output), "010");
    }

    #[test]
    fn ctc_decode_all_blank_is_empty() {
        let output = output_with(&[]);
        assert_eq!(ctc_decode(&output), "");
    }

    #[test]
    fn ctc_decode_repeats_needing_separator() {
        let output = output_with(&[(0, 1), (1, 0), (2, 1)]);
        assert_eq!(ctc_decode(&output), "00");
    }

    #[test]
    fn preprocess_handles_native_and_scaled_images() {
        use image::{ImageBuffer, Rgb};
        let native = ImageBuffer::from_pixel(200, 40, Rgb([128u8, 128, 128]));
        let mut native_bytes = Vec::new();
        image::DynamicImage::ImageRgb8(native)
            .write_to(&mut Cursor::new(&mut native_bytes), image::ImageFormat::Png)
            .unwrap();
        let arr = preprocess(&native_bytes).unwrap();
        assert_eq!(arr.shape(), &[1, 1, 40, 200]);

        let scaled = ImageBuffer::from_pixel(100, 20, Rgb([128u8, 128, 128]));
        let mut scaled_bytes = Vec::new();
        image::DynamicImage::ImageRgb8(scaled)
            .write_to(&mut Cursor::new(&mut scaled_bytes), image::ImageFormat::Png)
            .unwrap();
        let arr2 = preprocess(&scaled_bytes).unwrap();
        assert_eq!(arr2.shape(), &[1, 1, 40, 200]);
    }
}
